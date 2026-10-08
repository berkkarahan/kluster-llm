"""Device-level contiguous placement matching the pinned llama.cpp layer splitter."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal

from kllm.core.memory_estimation import (
    MIB,
    Layout,
    effective_launch_settings,
    memory_costs,
)
from kllm.core.tensor_split import proportional_weights
from kllm.core.contiguous_allocation import (
    ContiguousAllocation,
    allocation_rank,
    allocate_contiguous as _allocate_contiguous,
)
from kllm.core.placement_models import (
    DeviceAllocation,
    DevicePlacementPlan,
    NodeByteValue,
    NodeDeviceAssignment,
    PlacementNodeInput,
    PlacementDeviceCandidate,
    PlacementSettings,
)
from kllm.accel import unified_vram_fraction
from kllm.exceptions import ValidationError


Device = PlacementDeviceCandidate
Allocation = DeviceAllocation


@dataclass(frozen=True)
class ManualAllocationResult:
    """Manual placement result and an optional reason why it did not fit."""

    solution: ContiguousAllocation | None
    failure_reason: str | None = None


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
    shared_budgets: Mapping[str, int] | None = None,
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
                    shared_budgets=shared_budgets,
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
    return ManualAllocationResult(ContiguousAllocation(0, cpu_base, tuple(allocations)))


def _expert_offload(
    costs: Sequence[int],
    expert_costs: Sequence[int],
    *,
    local_devices: Sequence[Device],
    remote_devices: Sequence[Device],
    cpu_devices: Sequence[Device],
    cpu_budget: int,
    cpu_base: int,
    overhead: int,
    master_node_id: str,
    required_nodes: frozenset[str],
    shared_budgets: Mapping[str, int] | None,
    node_priority: Sequence[str],
    moe_offload: int | str,
) -> tuple[ContiguousAllocation | None, dict[str, Any]]:
    """Offload MoE expert weights to remote nodes while shared weights stay local.

    ponytail: v1 implements the "experts" strategy only — shared weights stay on
    the master GPU and whole expert groups move to remote GPU (then remote RAM).
    Whole-layer/mixed strategies fall through to the existing whole-layer
    allocator; add a finer per-expert cost model here if a real workload needs
    experts and attention on different nodes.
    """
    count = len(costs)
    layer_count = count - 1
    expert_layers = [i for i in range(layer_count) if expert_costs[i] > 0]
    if not expert_layers or moe_offload == 0:
        return None, {}
    if moe_offload == "auto":
        offloaded = expert_layers
    else:
        offloaded = expert_layers[: max(0, int(moe_offload))]
    if not offloaded:
        return None, {}
    offload_set = set(offloaded)
    shared_costs = tuple(
        costs[i] - expert_costs[i] if i in offload_set else costs[i] for i in range(count)
    )
    solution = _allocate_contiguous(
        local_devices,
        shared_costs,
        cpu_budget=cpu_budget,
        cpu_base=cpu_base,
        overhead=overhead,
        master_node_id=master_node_id,
        required_nodes=required_nodes,
        shared_budgets=shared_budgets,
        allow_cpu_spill=False,
        node_priority=node_priority,
    )
    if solution is None:
        return None, {}
    targets = list(remote_devices) + list(cpu_devices)
    used: dict[int, int] = {}
    layer_assign: dict[int, Device] = {}
    for layer in offloaded:
        ebytes = expert_costs[layer]
        placed = False
        for candidate in targets:
            if used.get(id(candidate), 0) + ebytes <= candidate.budget_bytes:
                layer_assign[layer] = candidate
                used[id(candidate)] = used.get(id(candidate), 0) + ebytes
                placed = True
                break
        if not placed:
            return None, {}
    assignments = tuple(
        (
            candidate.node_id,
            expert_costs[layer],
            candidate.memory_kind,
            candidate.rpc_endpoint,
            candidate.rpc_index,
            candidate.rpc_device_count,
        )
        for layer, candidate in layer_assign.items()
    )
    overrides = tuple(
        f"blk.{layer}.ffn_*=RPC[{candidate.rpc_endpoint}]"
        for layer, candidate in layer_assign.items()
        if candidate.rpc_endpoint
    )
    return solution, {
        "shared_costs": shared_costs,
        "offloaded_layers": tuple(offloaded),
        "assignments": assignments,
        "tensor_overrides": overrides,
        "n_cpu_moe": None,
    }


def _expert_offload_manual(
    nodes: Sequence[PlacementNodeInput],
    devices: Sequence[Device],
    cpu_devices: Sequence[Device],
    costs: Sequence[int],
    expert_costs: Sequence[int],
    node_shares: dict[str, float],
    *,
    cpu_budget: int,
    cpu_base: int,
    overhead: int,
    master_node_id: str,
    shared_budgets: Mapping[str, int] | None,
    moe_offload: int | str,
) -> tuple[ContiguousAllocation | None, dict[str, Any]]:
    """Expert offload under manual node shares; experts follow the share fractions."""
    count = len(costs)
    layer_count = count - 1
    expert_layers = [i for i in range(layer_count) if expert_costs[i] > 0]
    if not expert_layers or moe_offload == 0:
        return None, {}
    if moe_offload == "auto":
        offloaded = expert_layers
    else:
        offloaded = expert_layers[: max(0, int(moe_offload))]
    if not offloaded:
        return None, {}
    offload_set = set(offloaded)
    shared_costs = tuple(
        costs[i] - expert_costs[i] if i in offload_set else costs[i] for i in range(count)
    )
    manual = _allocate_manual(
        nodes,
        devices,
        shared_costs,
        node_shares,
        cpu_budget=cpu_budget,
        cpu_base=cpu_base,
        overhead=overhead,
        master_node_id=master_node_id,
        shared_budgets=shared_budgets,
    )
    if manual.solution is None:
        return None, {}
    active_nodes = [n for n in nodes if node_shares.get(n.node_id, 0) > 0]
    if not active_nodes:
        return None, {}
    total_expert = sum(expert_costs[i] for i in offloaded)
    target = {n.node_id: node_shares[n.node_id] * total_expert for n in active_nodes}
    node_gpu_used: dict[str, int] = {}
    for allocation in manual.solution.allocations:
        if allocation.memory_kind == "gpu":
            node_gpu_used[allocation.node_id] = (
                node_gpu_used.get(allocation.node_id, 0) + allocation.estimated_bytes
            )
    node_gpu_budget = {
        n.node_id: sum(d.budget_bytes for d in devices if d.node_id == n.node_id) for n in nodes
    }
    remaining = {
        n.node_id: node_gpu_budget.get(n.node_id, 0) - node_gpu_used.get(n.node_id, 0)
        for n in active_nodes
    }
    placed: dict[str, int] = {n.node_id: 0 for n in active_nodes}
    layer_assign: dict[int, Device] = {}
    for layer in offloaded:
        ebytes = expert_costs[layer]
        nid = max(active_nodes, key=lambda n: target[n.node_id] - placed[n.node_id]).node_id
        if remaining.get(nid, 0) < ebytes:
            return None, {}
        candidate = next((d for d in devices if d.node_id == nid), None)
        if candidate is None:
            return None, {}
        layer_assign[layer] = candidate
        placed[nid] += ebytes
        remaining[nid] -= ebytes
    assignments = tuple(
        (
            candidate.node_id,
            expert_costs[layer],
            candidate.memory_kind,
            candidate.rpc_endpoint,
            candidate.rpc_index,
            candidate.rpc_device_count,
        )
        for layer, candidate in layer_assign.items()
    )
    overrides = tuple(
        f"blk.{layer}.ffn_*=RPC[{candidate.rpc_endpoint}]"
        for layer, candidate in layer_assign.items()
        if candidate.rpc_endpoint
    )
    return manual.solution, {
        "shared_costs": shared_costs,
        "offloaded_layers": tuple(offloaded),
        "assignments": assignments,
        "tensor_overrides": overrides,
        "n_cpu_moe": None,
    }


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
    required_nodes: Sequence[str] = (),
    node_priority: Sequence[str] = (),
) -> DevicePlacementPlan:
    required = frozenset(required_nodes)
    unknown = required - {n.node_id for n in nodes}
    if unknown:
        raise ValidationError(f"required nodes are unavailable: {', '.join(sorted(unknown))}")
    if required and (node_shares is not None or settings.split_mode != "layer"):
        raise ValidationError("required_nodes require automatic layer placement")
    if len(set(node_priority)) != len(node_priority) or any(
        not nid.strip() or nid != nid.strip() for nid in node_priority
    ):
        raise ValidationError("node_priority must contain unique nonempty node IDs")
    unavailable = set(node_priority) - {n.node_id for n in nodes}
    if unavailable:
        raise ValidationError(f"priority nodes are unavailable: {', '.join(sorted(unavailable))}")
    if node_priority and (node_shares is not None or settings.split_mode != "layer"):
        raise ValidationError("node_priority requires automatic layer placement")
    settings = effective_launch_settings(settings, context)
    context = settings.context
    device_count = sum(len(n.devices) for n in nodes) if device == "gpu" else 0
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
            int(n.total_ram_bytes * min(headroom, n.ram_fraction)),
            n.available_ram_bytes + n.reclaimable_ram_bytes,
        )

    cpu_budget = ram(master)
    inputs = memory.input_bytes + memory.auxiliary_weights_bytes
    cpu_base = inputs + memory.overhead + memory.checkpoint_bytes + memory.auxiliary_runtime_bytes
    devices: list[Device] = []
    if device == "gpu":
        for n in nodes:
            inventory = n.devices
            if not inventory and n.backend not in ("cpu", "unknown"):
                raise ValidationError(
                    f"{n.node_id}: device inventory missing; update/rejoin this node before planning"
                )
            # One machine-wide pool, split across this node's devices so two
            # GPUs cannot each claim 65% of the same RAM.
            unified_total = (
                int(n.total_ram_bytes * unified_vram_fraction())
                if n.unified_memory and n.total_ram_bytes > 0
                else None
            )
            per_device = (
                unified_total // len(inventory) if unified_total is not None and inventory else None
            )
            for i, inventory_device in enumerate(inventory):
                total = inventory_device.vram_bytes
                free = inventory_device.free_bytes
                if per_device is not None:
                    # Effective VRAM is a fixed share of host RAM, not the
                    # reported carve-out (macOS ~80%, Strix Halo often ~512MB).
                    total = per_device
                    free = per_device
                budget = min(
                    int(total * min(headroom, n.vram_fraction)),
                    free + inventory_device.reclaimable_bytes,
                )
                if n.unified_memory:
                    budget = min(budget, max(0, ram(n) - (cpu_base if n.is_master else 0)))
                local = n.is_master
                name = inventory_device.device
                if not name:
                    raise ValidationError(f"{n.node_id}: missing llama.cpp device name")
                endpoint = n.rpc_endpoint
                rpc_inventory = {d.device: d for d in n.rpc_devices}
                rpc_device = rpc_inventory.get(name)
                if (
                    not local
                    and n.rpc_devices
                    and (rpc_device is None or rpc_device.memory_kind != "gpu")
                ):
                    raise ValidationError(
                        f"{n.node_id}: selected GPU absent from running RPC inventory; rejoin this node"
                    )
                rpc_index = rpc_device.index if rpc_device is not None else i
                devices.append(
                    Device(
                        node_id=n.node_id,
                        device=name if local else f"RPC[{endpoint}]/{rpc_index}",
                        backend=inventory_device.backend or n.backend,
                        local=local,
                        budget_bytes=budget,
                        latency_ms=n.latency_ms,
                        rpc_endpoint=None if local else endpoint,
                        rpc_index=rpc_index,
                        rpc_device_count=len(n.rpc_devices) or len(inventory),
                        unified_memory=n.unified_memory,
                    )
                )
    cpu_devices: list[Device] = []
    if node_shares is None and settings.split_mode == "layer":
        for n in nodes:
            if n.is_master:
                continue  # Master CPU layers remain the upstream contiguous prefix.
            for rpc_device in n.rpc_devices:
                if rpc_device.memory_kind != "ram":
                    continue
                if not n.rpc_endpoint:
                    raise ValidationError(f"{n.node_id}: CPU RPC device requires an endpoint")
                cpu_devices.append(
                    Device(
                        node_id=n.node_id,
                        device=f"RPC[{n.rpc_endpoint}]/{rpc_device.index}",
                        backend="cpu",
                        local=False,
                        budget_bytes=ram(n),
                        latency_ms=n.latency_ms,
                        rpc_endpoint=n.rpc_endpoint,
                        rpc_index=rpc_device.index,
                        rpc_device_count=len(n.rpc_devices),
                        unified_memory=n.unified_memory,
                        memory_kind="ram",
                    )
                )
    max_cap = max((d.budget_bytes for d in devices), default=1) or 1
    max_lat = max((d.latency_ms for d in devices), default=0)
    devices = [
        replace(
            candidate,
            score=(
                vram_weight * candidate.budget_bytes / max_cap
                + latency_weight * (1 - candidate.latency_ms / max_lat if max_lat else 1)
            ),
        )
        for candidate in devices
    ]
    gpu_budgets = {
        n.node_id: (
            min(
                max(0, ram(n) - (cpu_base if n.is_master else 0)),
                sum(d.budget_bytes for d in devices if d.node_id == n.node_id),
            )
            if n.unified_memory
            else sum(d.budget_bytes for d in devices if d.node_id == n.node_id)
        )
        for n in nodes
    }
    devices.sort(key=lambda d: (-d.score, -d.budget_bytes))
    cpu_devices.sort(key=lambda d: (-d.budget_bytes, d.latency_ms))
    if node_priority:
        priority_order = {nid: i for i, nid in enumerate(node_priority)}
        devices.sort(key=lambda d: priority_order.get(d.node_id, len(priority_order)))
        cpu_devices.sort(key=lambda d: priority_order.get(d.node_id, len(priority_order)))
    gpu_devices = list(devices)
    shared_budgets = {
        n.node_id: max(0, ram(n) - (cpu_base if n.is_master else 0))
        for n in nodes
        if n.unified_memory
    }
    model_bytes = sum(costs)
    overhead = memory.overhead
    split_mode = settings.split_mode
    failure_reason: str | None = None
    routing_mode: Literal["manual", "local", "single_remote", "pool"] = "pool"
    expert_offloaded = False
    expert_strategy = "none"
    expert_offload_count: int | None = None
    expert_assignments: list[tuple[str, int, str, str | None, int, int]] = []
    tensor_overrides: list[str] = []
    n_cpu_moe: int | None = None
    if node_shares is not None:
        if device != "gpu" or split_mode != "layer":
            raise ValidationError("manual node shares require GPU layer placement")
        active = {n.node_id for n in nodes}
        if any(share > 0 for nid, share in node_shares.items() if nid not in active):
            raise ValidationError("manual node shares must match current cluster members")
        node_shares = {nid: node_shares.get(nid, 0) for nid in active}
        routing_mode = "manual"
        manual = _allocate_manual(
            nodes,
            devices,
            costs,
            node_shares,
            cpu_budget=cpu_budget,
            cpu_base=cpu_base,
            overhead=overhead,
            master_node_id=master.node_id,
            shared_budgets=shared_budgets,
        )
        solution = manual.solution
        failure_reason = manual.failure_reason
        if solution is None and memory.expert_bytes > 0 and settings.moe_offload != 0:
            solution, expert_meta = _expert_offload_manual(
                nodes,
                devices,
                cpu_devices,
                costs,
                memory.expert_costs,
                node_shares,
                cpu_budget=cpu_budget,
                cpu_base=cpu_base,
                overhead=overhead,
                master_node_id=master.node_id,
                shared_budgets=shared_budgets,
                moe_offload=settings.moe_offload,
            )
            if solution is not None:
                expert_offloaded = True
                expert_strategy = "experts"
                expert_offload_count = len(expert_meta["offloaded_layers"])
                expert_assignments = list(expert_meta["assignments"])
                tensor_overrides = list(expert_meta["tensor_overrides"])
                n_cpu_moe = expert_meta["n_cpu_moe"]
                costs = expert_meta["shared_costs"]
                failure_reason = None
    else:

        def allocate(candidates: Sequence[Device], *, spill: bool) -> ContiguousAllocation | None:
            return _allocate_contiguous(
                candidates,
                costs,
                cpu_budget=cpu_budget,
                cpu_base=cpu_base,
                overhead=overhead,
                master_node_id=master.node_id,
                required_nodes=required,
                shared_budgets=shared_budgets,
                allow_cpu_spill=spill,
                node_priority=node_priority,
            )

        # GPU-first: a local CPU-spill shortcut must not hide a feasible GPU pool.
        locals_only = [d for d in devices if d.local]
        solution = allocate(locals_only, spill=False) if locals_only and not node_priority else None
        if solution is not None:
            routing_mode, devices = "local", locals_only
        if solution is None and not node_priority:
            for remote in sorted(
                (d for d in gpu_devices if not d.local), key=lambda d: -d.budget_bytes
            ):
                single = allocate([remote], spill=False)
                if single is not None:
                    solution, routing_mode, devices = single, "single_remote", [remote]
                    break
        if solution is None:
            devices = gpu_devices
            solution = allocate(devices, spill=False)
        if solution is None and memory.expert_bytes > 0 and settings.moe_offload != 0:
            solution, expert_meta = _expert_offload(
                costs,
                memory.expert_costs,
                local_devices=[d for d in gpu_devices if d.local],
                remote_devices=[d for d in gpu_devices if not d.local],
                cpu_devices=cpu_devices,
                cpu_budget=cpu_budget,
                cpu_base=cpu_base,
                overhead=overhead,
                master_node_id=master.node_id,
                required_nodes=required,
                shared_budgets=shared_budgets,
                node_priority=node_priority,
                moe_offload=settings.moe_offload,
            )
            if solution is not None:
                expert_offloaded = True
                expert_strategy = "experts"
                expert_offload_count = len(expert_meta["offloaded_layers"])
                expert_assignments = list(expert_meta["assignments"])
                tensor_overrides = list(expert_meta["tensor_overrides"])
                n_cpu_moe = expert_meta["n_cpu_moe"]
                costs = expert_meta["shared_costs"]
                devices = gpu_devices
                endpoints_used = {a.rpc_endpoint for a in solution.allocations if a.rpc_endpoint}
                routing_mode = (
                    "local"
                    if not endpoints_used
                    else "single_remote"
                    if len(endpoints_used) == 1
                    else "pool"
                )
        if solution is None:
            devices = gpu_devices + cpu_devices
            solution = allocate(devices, spill=True)
            # Try GPU and CPU orders in both directions; CPU devices may need to
            # precede a GPU to host an expensive early layer without CPU-prefix spill.
            if cpu_devices and gpu_devices:
                alternate = allocate(cpu_devices + gpu_devices, spill=True)
                if alternate is not None and (
                    solution is None
                    or allocation_rank(alternate, costs, node_priority, master.node_id)
                    < allocation_rank(solution, costs, node_priority, master.node_id)
                ):
                    solution = alternate
            if solution is not None:
                endpoints_used = {a.rpc_endpoint for a in solution.allocations if a.rpc_endpoint}
                routing_mode = (
                    "local"
                    if not endpoints_used
                    else "single_remote"
                    if len(endpoints_used) == 1
                    else "pool"
                )
        if solution is None:
            details = []
            if cpu_base > cpu_budget:
                details.append(
                    "master RAM cannot hold input tensors, checkpoints and runtime reserves"
                )
            if required:
                details.append(
                    f"cannot allocate a model layer on every required node ({', '.join(sorted(required))})"
                )
            if any(n.available_ram_bytes == 0 for n in nodes if n.unified_memory):
                details.append(
                    "a unified-memory node reports zero available RAM; check its capacity measurement"
                )
            failure_reason = (
                "; ".join(details) or "no contiguous device allocation fits available memory"
            )

    if node_priority and solution is not None:
        endpoints_used = {a.rpc_endpoint for a in solution.allocations if a.rpc_endpoint}
        routing_mode = (
            "local"
            if not endpoints_used
            else "single_remote"
            if len(endpoints_used) == 1
            else "pool"
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
            id(allocation): replace(allocation, device=f"RPC{rpc_offset + allocation.rpc_index}")
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
        nid: (sum(costs[:cpu_count]) if nid == master.node_id and fits else 0)
        + sum(sum(costs[a.layer_start : a.layer_end]) for a in allocations if a.node_id == nid)
        for nid in node_ids
    }
    ram_budgets = {
        n.node_id: ram(n) if n.is_master or any(d.node_id == n.node_id for d in cpu_devices) else 0
        for n in nodes
    }
    node_ram = {
        n.node_id: (cpu_bytes if n.is_master else 0)
        + sum(
            a.estimated_bytes
            for a in allocations
            if a.node_id == n.node_id and a.memory_kind == "ram"
        )
        for n in nodes
    }
    node_vram = {
        n.node_id: sum(
            a.estimated_bytes
            for a in allocations
            if a.node_id == n.node_id and a.memory_kind == "gpu"
        )
        for n in nodes
    }
    for nid, ebytes, kind, _endpoint, _rpc_index, _rpc_count in expert_assignments:
        if nid in node_ids:
            idx = node_ids.index(nid)
            node_alloc[idx] += ebytes
            node_workload[nid] = node_workload.get(nid, 0) + ebytes
            if kind == "gpu":
                node_vram[nid] = node_vram.get(nid, 0) + ebytes
            else:
                node_ram[nid] = node_ram.get(nid, 0) + ebytes
    node_budgets = {
        n.node_id: max(ram_budgets[n.node_id], gpu_budgets[n.node_id])
        if n.unified_memory
        else ram_budgets[n.node_id] + gpu_budgets[n.node_id]
        for n in nodes
    }
    budget = sum(node_budgets.values())
    tight = fits and (
        cpu_bytes > cpu_budget * 0.9
        or any(a.estimated_bytes > a.budget_bytes * 0.9 for a in allocations)
        or any(
            node_ram[n.node_id] + node_vram[n.node_id] > node_budgets[n.node_id] * 0.9
            for n in nodes
            if n.unified_memory
        )
    )
    fit_status: Literal["overflow", "tight", "fits"] = (
        "overflow" if not fits else "tight" if tight else "fits"
    )
    device_effective = "gpu" if any(a.memory_kind == "gpu" for a in allocations) else "cpu"
    return DevicePlacementPlan(
        required_nodes=tuple(sorted(required)),
        node_priority=tuple(node_priority),
        node_ram_budget_bytes=tuple(
            NodeByteValue(nid, value) for nid, value in ram_budgets.items()
        ),
        node_ram_alloc_bytes=tuple(NodeByteValue(nid, value) for nid, value in node_ram.items()),
        node_vram_alloc_bytes=tuple(NodeByteValue(nid, value) for nid, value in node_vram.items()),
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
        budgets_bytes=tuple(node_budgets[n.node_id] for n in nodes),
        tensor_split=tuple(split),
        tensor_split_csv=",".join(f"{v:.6f}" for v in split) or "0",
        device_allocations=tuple(allocations),
        device_order=tuple(device_order),
        rpc_endpoints=tuple(endpoints),
        routing_mode=routing_mode,
        cpu_layers=cpu_count,
        cpu_alloc_bytes=sum(node_ram.values()),
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
                    "gpu"
                    if any(a.node_id == nid and a.memory_kind == "gpu" for a in allocations)
                    else "cpu"
                ),
                fallback=False,
            )
            for nid in node_ids
        ),
        node_scores=tuple(
            max((d.score for d in devices if d.node_id == nid), default=0) for nid in node_ids
        ),
        node_latency_ms=tuple(n.latency_ms for n in nodes),
        vram_alloc_bytes=sum(node_vram.values()),
        ram_alloc_bytes=sum(node_ram.values()),
        fit_status=fit_status,
        hint=(failure_reason or "no contiguous device allocation fits available memory")
        if not fits
        else "allocation exceeds 90% of a memory budget"
        if tight
        else "",
        expert_offloaded=expert_offloaded,
        expert_strategy=expert_strategy,
        expert_offload_count=expert_offload_count,
        tensor_overrides=tuple(tensor_overrides),
        n_cpu_moe=n_cpu_moe,
    )
