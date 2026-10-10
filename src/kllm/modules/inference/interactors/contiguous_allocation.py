"""Contiguous layer allocation with node requirements and shared-memory limits."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from kllm.modules.inference.interactors.placement_models import DeviceAllocation, PlacementDeviceCandidate


@dataclass(frozen=True)
class ContiguousAllocation:
    cpu_layers: int
    cpu_bytes: int
    allocations: tuple[DeviceAllocation, ...]


def allocation_rank(
    solution: ContiguousAllocation,
    costs: Sequence[int],
    node_priority: Sequence[str] = (),
    master_node_id: str = "",
) -> tuple[int, tuple[int, ...], int, float, int]:
    """Minimize CPU use, maximize ordered node workloads, then favor locality and score."""
    allocations = solution.allocations
    return (
        solution.cpu_bytes + sum(a.estimated_bytes for a in allocations if a.memory_kind == "ram"),
        tuple(
            -(sum(costs[: solution.cpu_layers]) if nid == master_node_id else 0)
            - sum(sum(costs[a.layer_start : a.layer_end]) for a in allocations if a.node_id == nid)
            for nid in node_priority
        ),
        len({a.rpc_endpoint for a in allocations if a.rpc_endpoint}),
        -sum(a.score * sum(costs[a.layer_start : a.layer_end]) for a in allocations),
        len(allocations),
    )


@dataclass(frozen=True)
class _State:
    allocations: tuple[DeviceAllocation, ...]
    shared_used: tuple[int, ...]
    cpu_bytes: int = 0
    score: float = 0
    priority_bytes: tuple[int, ...] = ()

    @property
    def rank(self) -> tuple[int, tuple[int, ...], float, int]:
        return self.cpu_bytes, self.priority_bytes, -self.score, len(self.allocations)


def allocate_contiguous(
    devices: Sequence[PlacementDeviceCandidate],
    costs: Sequence[int],
    *,
    cpu_budget: int,
    cpu_base: int,
    overhead: int,
    master_node_id: str,
    required_nodes: frozenset[str] = frozenset(),
    shared_budgets: Mapping[str, int] | None = None,
    allow_cpu_spill: bool = True,
    node_priority: Sequence[str] = (),
) -> ContiguousAllocation | None:
    count = len(costs)
    if len(required_nodes) > count - 1 or cpu_base > cpu_budget:
        return None
    prefix = [0]
    for cost in costs:
        prefix.append(prefix[-1] + cost)
    required_bits = {nid: 1 << i for i, nid in enumerate(sorted(required_nodes))}
    full_mask = (1 << len(required_bits)) - 1
    pools = dict(shared_budgets or {})
    for d in devices:
        if d.unified_memory:
            pools.setdefault(d.node_id, d.budget_bytes)
    pool_ids = tuple(pools)
    pool_index = {nid: i for i, nid in enumerate(pool_ids)}
    last_device = {d.node_id: i for i, d in enumerate(devices)}
    best: ContiguousAllocation | None = None
    best_rank: tuple[int, tuple[int, ...], int, float, int] | None = None
    for cpu_count in range(count + 1 if allow_cpu_spill else 1):
        cpu_bytes = cpu_base + prefix[cpu_count]
        if cpu_bytes > cpu_budget or (best_rank is not None and cpu_bytes > best_rank[0]):
            break
        initial_used = tuple(prefix[cpu_count] if nid == master_node_id else 0 for nid in pool_ids)
        if any(used > pools[nid] for nid, used in zip(pool_ids, initial_used, strict=True)):
            continue
        covered = required_bits.get(master_node_id, 0) if cpu_count else 0
        # Different required-node coverage, RPC endpoint sets and shared-pool use
        # must survive at the same layer boundary. A boundary alone is not a state.
        states: dict[tuple[int, int, frozenset[str]], list[_State]] = {
            (cpu_count, covered, frozenset()): [
                _State((), initial_used, priority_bytes=tuple(0 for _ in node_priority))
            ]
        }
        for index, d in enumerate(devices):
            next_states = {key: list(value) for key, value in states.items()}
            for (start, mask, endpoints), alternatives in states.items():
                for state in alternatives:
                    budget = d.budget_bytes
                    pool = pool_index.get(d.node_id) if d.unified_memory else None
                    if pool is not None:
                        budget = min(budget, pools[d.node_id] - state.shared_used[pool])
                    for end in range(start + 1, count + 1):
                        used = prefix[end] - prefix[start] + overhead
                        if used > budget:
                            break
                        shared = list(state.shared_used)
                        if pool is not None:
                            shared[pool] += used
                        allocation = d.allocate(
                            layer_start=start,
                            layer_end=end,
                            estimated_bytes=used,
                            total_layers=count,
                        )
                        candidate = _State(
                            state.allocations + (allocation,),
                            tuple(shared),
                            state.cpu_bytes + (used if d.memory_kind == "ram" else 0),
                            state.score + d.score * (prefix[end] - prefix[start]),
                            tuple(
                                value - (prefix[end] - prefix[start] if nid == d.node_id else 0)
                                for nid, value in zip(
                                    node_priority, state.priority_bytes, strict=True
                                )
                            ),
                        )
                        new_mask = mask | (
                            required_bits.get(d.node_id, 0) if start < count - 1 else 0
                        )
                        new_endpoints = (
                            endpoints | {d.rpc_endpoint} if d.rpc_endpoint else endpoints
                        )
                        key = (end, new_mask, frozenset(new_endpoints))
                        bucket = next_states.setdefault(key, [])
                        if any(
                            other.rank <= candidate.rank
                            and all(
                                a <= b
                                for a, b in zip(
                                    other.shared_used, candidate.shared_used, strict=True
                                )
                            )
                            for other in bucket
                        ):
                            continue
                        bucket[:] = [
                            other
                            for other in bucket
                            if not (
                                candidate.rank <= other.rank
                                and all(
                                    a <= b
                                    for a, b in zip(
                                        candidate.shared_used, other.shared_used, strict=True
                                    )
                                )
                            )
                        ]
                        bucket.append(candidate)
            states = {
                key: value
                for key, value in next_states.items()
                if all(
                    key[1] & bit or last_device.get(nid, -1) > index
                    for nid, bit in required_bits.items()
                )
            }
        for (end, mask, _), alternatives in states.items():
            if end != count or mask != full_mask:
                continue
            for state in alternatives:
                candidate_solution = ContiguousAllocation(cpu_count, cpu_bytes, state.allocations)
                rank = allocation_rank(candidate_solution, costs, node_priority, master_node_id)
                if best_rank is None or rank < best_rank:
                    best, best_rank = candidate_solution, rank
    return best
