"""Reserve independently retained Prima visit schedulers in each memory pool."""

from collections import defaultdict
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class PrimaGraphReserve:
    allocations: tuple
    host_extra: dict[str, int]
    extra_units: int
    graph_counts: dict[str, int]


def reserve_graph_visits(allocations, cpu_layers, total_layers, master, overhead):
    """Add conservative graph reserves without changing any assigned layer.

    The shared allocator already charges one host graph on the master and one
    graph per device allocation. Distributed Prima retains input/output graphs
    and each contiguous rank visit independently. Each additional graph is
    charged the full model scratch estimate and runtime overhead; this is a
    capacity estimate, not a measurement of backend buffer reuse.
    """
    owners = [None] * total_layers
    for layer in range(min(cpu_layers, total_layers)):
        owners[layer] = master
    for allocation in allocations:
        for layer in range(allocation.layer_start, min(allocation.layer_end, total_layers)):
            if owners[layer] is not None:
                raise ValueError("Prima graph reservation encountered overlapping ownership")
            owners[layer] = allocation.node_id
    if any(owner is None for owner in owners):
        raise ValueError("Prima graph reservation requires complete transformer ownership")
    nodes = set(owners) | {master}
    if len(nodes) == 1:
        return PrimaGraphReserve(tuple(allocations), {}, 0, {master: 1})
    visits = []
    for layer, owner in enumerate(owners):
        if not visits or visits[-1][2] != owner:
            visits.append((layer, layer + 1, owner))
        else:
            start, _, node = visits[-1]
            visits[-1] = (start, layer + 1, node)
    graphs = {
        node: sum(owner == node for _, _, owner in visits) + (1 if node == master else 0)
        for node in nodes
    }
    has_ram_alloc = {
        node: any(a.node_id == node and a.memory_kind == "ram" for a in allocations)
        for node in nodes
    }
    # Master cpu_base already includes 1x overhead; do not add 2x overhead on top to master RAM.
    # Eliminate host overhead charges on pure GPU workers.
    host_units = {}
    for node in nodes:
        if node == master:
            master_host_visits = sum(
                owner == master
                and any(
                    a.node_id == master
                    and a.memory_kind == "ram"
                    and a.layer_start < end
                    and a.layer_end > start
                    for a in allocations
                )
                for start, end, owner in visits
            )
            host_units[node] = max(0, master_host_visits - 1)
        else:
            if has_ram_alloc.get(node, False):
                host_units[node] = max(1, sum(owner == node for _, _, owner in visits))
            else:
                host_units[node] = 0
    by_device = defaultdict(list)
    for index, allocation in enumerate(allocations):
        if allocation.memory_kind == "gpu":
            by_device[(allocation.node_id, allocation.device)].append(index)
    updated = list(allocations)
    units = sum(host_units.values())
    for (node, _), indices in by_device.items():
        gpu_visits = sum(
            owner == node and any(allocations[index].layer_start < end
                                 and allocations[index].layer_end > start for index in indices)
            for start, end, owner in visits
        )
        output = any(allocations[index].layer_start <= total_layers < allocations[index].layer_end
                     for index in indices)
        extra = max(0, gpu_visits + int(output) - len(indices))
        if extra:
            first = indices[0]
            updated[first] = replace(updated[first], estimated_bytes=updated[first].estimated_bytes + extra * overhead)
            units += extra
    return PrimaGraphReserve(tuple(updated), {node: count * overhead for node, count in host_units.items()}, units, graphs)
