"""Device-level contiguous placement matching the pinned llama.cpp layer splitter."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Literal

from kllm.core.memory_estimation import (
    MIB,
    Layout,
    effective_launch_settings,
    memory_costs,
)
from kllm.core.tensor_split import proportional_weights, select_routing_devices
from kllm.core.placement_models import (
    DeviceAllocation,
    DevicePlacementPlan,
    NodeByteValue,
    NodeDeviceAssignment,
    PlacementNodeInput,
    PlacementDeviceCandidate,
    PlacementSettings,
)
from kllm.exceptions import ValidationError


Device = PlacementDeviceCandidate
Allocation = DeviceAllocation


@dataclass(frozen=True)
class ContiguousAllocation:
    """A successful contiguous layer placement with its CPU prefix."""

    cpu_layers: int
    cpu_bytes: int
    allocations: tuple[Allocation, ...]


@dataclass(frozen=True)
class ManualAllocationResult:
    """Manual placement result and an optional reason why it did not fit."""

    solution: ContiguousAllocation | None
    failure_reason: str | None = None


def _allocate_contiguous(
    devices: Sequence[Device],
    costs: Sequence[int],
    *,
    cpu_budget: int,
    cpu_base: int,
    overhead: int,
    master_node_id: str,
) -> ContiguousAllocation | None:
    """Find a contiguous layer allocation, including its CPU prefix."""
    count = len(costs)
    prefix = [0]
    for cost in costs:
        prefix.append(prefix[-1] + cost)
    for cpu_count in range(count + 1):
        cpu_bytes = cpu_base + prefix[cpu_count]
        if cpu_bytes > cpu_budget:
            continue
        states: dict[int, list[Allocation]] = {cpu_count: []}
        for d in devices:
            next_states = dict(states)
            for start, assignments in states.items():
                budget = d.budget_bytes
                if d.local and d.unified_memory:
                    budget = max(0, budget - prefix[cpu_count])
                if d.unified_memory:
                    budget = max(
                        0,
                        budget
                        - sum(
                            a.estimated_bytes
                            for a in assignments
                            if a.node_id == d.node_id
                        ),
                    )
                for end in range(start + 1, count + 1):
                    used = prefix[end] - prefix[start] + overhead
                    if used > budget:
                        break
                    if end not in next_states:
                        next_states[end] = assignments + [
                            d.allocate(
                                layer_start=start,
                                layer_end=end,
                                estimated_bytes=used,
                                total_layers=count,
                            )
                        ]
            states = next_states
        if count in states:
            return ContiguousAllocation(cpu_count, cpu_bytes, tuple(states[count]))
    return None


def _allocate_manual(
    nodes: Sequence[PlacementNodeInput],
    devices: Sequence[Device],
    costs: Sequence[int],
    shares: dict[str, float],
    *,
    cpu_budget: int,
    cpu_base: int,
    overhead: int,
    master_node_id: str,
) -> ManualAllocationResult:
    """Round node byte targets to contiguous whole layers; never spill onto CPU."""
    if cpu_base > cpu_budget:
        return ManualAllocationResult(None, "master CPU base exceeds available RAM")
    prefix = [0]
    for cost in costs:
        prefix.append(prefix[-1] + cost)
    tolerance = max(costs) / 2 + 1
    targets = []
    target = 0.0
    for node in nodes:
        target += shares[node.node_id] * prefix[-1]
        targets.append(target)

    from functools import lru_cache

    @lru_cache(None)
    def assign(index: int, start: int) -> list[Allocation] | None:
        if index == len(nodes):
            return [] if start == len(costs) else None
        nid = nodes[index].node_id
        eligible = [d for d in devices if d.node_id == nid]
        ends = (
            [len(costs)]
            if index == len(nodes) - 1
            else [start]
            if shares[nid] == 0
            else sorted(
                range(start, len(costs) + 1), key=lambda end: abs(prefix[end] - targets[index])
            )
        )
        for end in ends:
            # ponytail: targets are rounded within half the largest indivisible
            # layer; a true byte-accurate split needs tensor (not layer) placement.
            if abs(prefix[end] - prefix[start] - shares[nid] * prefix[-1]) > tolerance:
                continue
            if end == start:
                here = []
            elif eligible:
                solved = _allocate_contiguous(
                    eligible,
                    costs[start:end],
                    cpu_budget=0,
                    cpu_base=0,
                    overhead=overhead,
                    master_node_id=master_node_id,
                )
                if solved is None or solved.cpu_layers != 0:
                    continue
                here = [
                    replace(
                        allocation,
                        layer_start=allocation.layer_start + start,
                        layer_end=allocation.layer_end + start,
                        output_layer=allocation.layer_end + start == len(costs),
                    )
                    for allocation in solved.allocations
                ]
            else:
                continue
            rest = assign(index + 1, end)
            if rest is not None:
                return here + rest
        return None

    allocations = assign(0, 0)
    if allocations is None:
        selected = ", ".join(n.node_id for n in nodes if shares[n.node_id] > 0)
        return ManualAllocationResult(
            None,
            f"requested node shares cannot fit GPU capacities with contiguous layers ({selected})",
        )
    return ManualAllocationResult(
        ContiguousAllocation(0, cpu_base, tuple(allocations))
    )


def plan_devices(
    nodes: Sequence[PlacementNodeInput],
    size: int,
    layout: Layout | None,
    settings: PlacementSettings,
    *,
    context: int,
    headroom: float,
    device: str,
    vram_weight: float,
    latency_weight: float,
    node_shares: dict[str, float] | None = None,
) -> DevicePlacementPlan:
    settings = effective_launch_settings(settings, context)
    context = settings.context
    device_count = (
        sum(len(n.devices) for n in nodes) if device == "gpu" else 0
    )
    settings = replace(settings, device_count=device_count)
    memory = memory_costs(layout, size, context, settings)
    costs = memory.costs
    count = len(costs)
    master = next((n for n in nodes if n.is_master), None)
    if master is None:
        raise ValidationError("master capacity is required for input tensors and CPU spill")

    def ram(n: PlacementNodeInput) -> int:
        # Only measured allocations of the process being replaced may be reclaimed.
        return min(
            int(n.total_ram_bytes * headroom),
            n.available_ram_bytes + n.reclaimable_ram_bytes,
        )

    cpu_budget = ram(master)
    inputs = memory.input_bytes + memory.auxiliary_weights_bytes
    cpu_base = (
        inputs + memory.overhead + memory.checkpoint_bytes + memory.auxiliary_runtime_bytes
    )
    devices: list[Device] = []
    if device == "gpu":
        for n in nodes:
            inventory = n.devices
            if not inventory and n.backend not in ("cpu", "unknown"):
                raise ValidationError(
                    f"{n.node_id}: device inventory missing; update/rejoin this node before planning"
                )
            for i, inventory_device in enumerate(inventory):
                total = inventory_device.vram_bytes
                free = inventory_device.free_bytes
                budget = min(
                    int(total * headroom), free + inventory_device.reclaimable_bytes
                )
                if n.unified_memory:
                    budget = min(budget, ram(n))
                    if n.is_master:
                        budget = max(0, budget - cpu_base)
                local = n.is_master
                name = inventory_device.device
                if not name:
                    raise ValidationError(f"{n.node_id}: missing llama.cpp device name")
                endpoint = n.rpc_endpoint
                devices.append(
                    Device(
                        node_id=n.node_id,
                        device=name if local else f"RPC[{endpoint}]/{i}",
                        backend=inventory_device.backend or n.backend,
                        local=local,
                        budget_bytes=budget,
                        latency_ms=n.latency_ms,
                        rpc_endpoint=None if local else endpoint,
                        rpc_index=i,
                        rpc_device_count=len(inventory),
                        unified_memory=n.unified_memory,
                    )
                )
    max_cap = max((d.budget_bytes for d in devices), default=1) or 1
    max_lat = max((d.latency_ms for d in devices), default=0)
    devices = [
        replace(
            candidate,
            score=(
                vram_weight * candidate.budget_bytes / max_cap
                + latency_weight
                * (1 - candidate.latency_ms / max_lat if max_lat else 1)
            ),
        )
        for candidate in devices
    ]
    gpu_budgets = {
        n.node_id: (
            min(ram(n), sum(d.budget_bytes for d in devices if d.node_id == n.node_id))
            if n.unified_memory
            else sum(d.budget_bytes for d in devices if d.node_id == n.node_id)
        )
        for n in nodes
    }
    devices.sort(key=lambda d: -d.score)

    model_bytes = sum(costs)
    overhead = memory.overhead
    locals_only = [d for d in devices if d.local]
    local_solution = (
        _allocate_contiguous(
            locals_only,
            costs,
            cpu_budget=cpu_budget,
            cpu_base=cpu_base,
            overhead=overhead,
            master_node_id=master.node_id,
        )
        if cpu_base <= cpu_budget and locals_only
        else None
    )
    locals_fit = cpu_base <= cpu_budget and bool(locals_only) and local_solution is not None
    if node_shares is not None:
        if device != "gpu" or settings.split_mode != "layer":
            raise ValidationError("manual node shares require GPU layer placement")
        active = {n.node_id for n in nodes}
        if any(share > 0 for nid, share in node_shares.items() if nid not in active):
            raise ValidationError("manual node shares must match current cluster members")
        node_shares = {nid: node_shares.get(nid, 0) for nid in active}
        routing_mode: Literal["manual", "local", "single_remote", "pool"] = "manual"
    else:
        routing = select_routing_devices(
            devices,
            model_bytes=model_bytes,
            overhead=overhead,
            cpu_base=cpu_base,
            cpu_budget=cpu_budget,
            locals_fit=locals_fit,
        )
        routing_mode = routing.mode
        devices = list(routing.devices)

    split_mode = settings.split_mode
    # For each possible CPU prefix, find contiguous device segments by dynamic programming.
    # Includes the output layer, so ngl and tensor_split describe the same allocation.
    failure_reason: str | None = None
    if node_shares is not None:
        manual = _allocate_manual(
            nodes,
            devices,
            costs,
            node_shares,
            cpu_budget=cpu_budget,
            cpu_base=cpu_base,
            overhead=overhead,
            master_node_id=master.node_id,
        )
        solution = manual.solution
        failure_reason = manual.failure_reason
    else:
        solution = (
            local_solution
            if routing_mode == "local"
            else _allocate_contiguous(
                devices,
                costs,
                cpu_budget=cpu_budget,
                cpu_base=cpu_base,
                overhead=overhead,
                master_node_id=master.node_id,
            )
        )
    fits = solution is not None
    if solution is None:
        cpu_count = count
        cpu_bytes = cpu_base + sum(costs)
        allocations: list[Allocation] = []
    else:
        cpu_count = solution.cpu_layers
        cpu_bytes = solution.cpu_bytes
        allocations = list(solution.allocations)
    ngl = count - cpu_count
    # Slightly bias interior boundaries downward to avoid float rounding assigning
    # a boundary layer to the previous device in upstream upper_bound(float).
    split: list[float] = [float(a.layers) for a in allocations]
    if len(split) > 1:
        split[0] -= 0.0001
        split[-1] += 0.0001
    endpoints = list(dict.fromkeys(a.rpc_endpoint for a in allocations if a.rpc_endpoint))
    rpc_offset = 0
    for endpoint in endpoints:
        selected = [a for a in allocations if a.rpc_endpoint == endpoint]
        renamed = {
            id(allocation): replace(
                allocation, device=f"RPC{rpc_offset + allocation.rpc_index}"
            )
            for allocation in selected
        }
        allocations = [renamed.get(id(allocation), allocation) for allocation in allocations]
        rpc_offset += selected[0].rpc_device_count
    if split_mode in ("row", "tensor"):
        # These modes use every selected device for each offloaded layer. The
        # layer DP remains the capacity estimate, while llama.cpp receives
        # proportional shares for the selected devices.
        split = proportional_weights(devices)
        device_order = [d.device for d in devices]
        endpoints = list(dict.fromkeys(d.rpc_endpoint for d in devices if d.rpc_endpoint))
    else:
        device_order = [a.device for a in allocations]
    node_ids = [n.node_id for n in nodes]
    node_alloc = [
        sum(a.estimated_bytes for a in allocations if a.node_id == nid)
        + (cpu_bytes if nid == master.node_id else 0)
        for nid in node_ids
    ]
    node_workload = {
        nid: sum(
            sum(costs[a.layer_start : a.layer_end])
            for a in allocations
            if a.node_id == nid
        )
        for nid in node_ids
    }
    budget = cpu_budget + sum(
        d.budget_bytes for d in devices if not (d.local and d.unified_memory)
    )
    tight = fits and (
        cpu_bytes > cpu_budget * 0.9
        or any(a.estimated_bytes > a.budget_bytes * 0.9 for a in allocations)
    )
    fit_status: Literal["overflow", "tight", "fits"] = (
        "overflow" if not fits else "tight" if tight else "fits"
    )
    device_effective = "gpu" if ngl else "cpu"
    return DevicePlacementPlan(
        node_ids=tuple(node_ids),
        node_alloc_bytes=tuple(node_alloc),
        distributable_bytes=model_bytes,
        node_workload_bytes=tuple(
            NodeByteValue(node_id, value) for node_id, value in node_workload.items()
        ),
        node_gpu_budget_bytes=tuple(
            NodeByteValue(node_id, value) for node_id, value in gpu_budgets.items()
        ),
        node_target_bytes=(
            tuple(NodeByteValue(nid, round(node_shares[nid] * model_bytes)) for nid in node_ids)
            if node_shares is not None
            else None
        ),
        budgets_bytes=tuple(
            ram(n)
            if n.is_master
            else sum(d.budget_bytes for d in devices if d.node_id == n.node_id)
            for n in nodes
        ),
        tensor_split=tuple(split),
        tensor_split_csv=",".join(f"{v:.6f}" for v in split) or "0",
        device_allocations=tuple(allocations),
        device_order=tuple(device_order),
        rpc_endpoints=tuple(endpoints),
        routing_mode=routing_mode,
        cpu_layers=cpu_count,
        cpu_alloc_bytes=cpu_bytes,
        ngl=ngl,
        estimated_layers=count - 1,
        model_size_bytes=size,
        context_effective=context,
        kv_estimate_bytes=memory.kv_bytes,
        recurrent_state_bytes=memory.state_bytes,
        checkpoint_estimate_bytes=memory.checkpoint_bytes,
        weights_estimate_bytes=memory.weights_bytes,
        auxiliary_weights_bytes=memory.auxiliary_weights_bytes,
        compute_estimate_bytes=memory.compute_bytes * (len(allocations) + 1),
        auxiliary_runtime_bytes=memory.auxiliary_runtime_bytes,
        execution_overhead_bytes=256 * MIB * (len(allocations) + 1),
        estimate_complete=memory.complete,
        estimate_warnings=memory.warnings,
        estimated_bytes=sum(node_alloc),
        total_budget_bytes=budget,
        budget_bytes=budget,
        estimate_confidence=memory.confidence,
        architecture=memory.architecture,
        split_mode=split_mode,
        device_requested=device,
        device_effective=device_effective,
        node_devices=tuple(
            NodeDeviceAssignment(
                node_id=nid,
                effective=(
                    "gpu" if any(a.node_id == nid for a in allocations) else "cpu"
                ),
                fallback=False,
            )
            for nid in node_ids
        ),
        node_scores=tuple(
            max((d.score for d in devices if d.node_id == nid), default=0) for nid in node_ids
        ),
        node_latency_ms=tuple(n.latency_ms for n in nodes),
        vram_alloc_bytes=sum(a.estimated_bytes for a in allocations),
        ram_alloc_bytes=cpu_bytes,
        fit_status=fit_status,
        hint=(failure_reason or "no contiguous device allocation fits available memory")
        if not fits
        else "allocation exceeds 90% of a memory budget"
        if tight
        else "",
    )
