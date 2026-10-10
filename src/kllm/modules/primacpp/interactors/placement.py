"""Translate Kluster placement without changing layer ownership or device order."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from kllm.modules.primacpp.entities import LayerRange, PrimaTopology, RankAllocation


def topology_from_plan(plan: dict, members: dict, runtime_id: str) -> PrimaTopology:
    if plan.get("fit_status") == "overflow":
        raise ValueError("Cannot create a Prima execution topology for an overflowing plan")
    if plan.get("split_mode", "layer") != "layer":
        raise ValueError("Prima row/tensor execution needs compatibility implementation")
    if plan.get("tensor_overrides") or plan.get("expert_offloaded"):
        raise ValueError("Prima remote MoE tensor placement needs compatibility implementation")
    masters = [node for node, member in members.items() if member.get("role") == "master"]
    if len(masters) != 1:
        raise ValueError("Prima requires exactly one current Kluster master")
    master = masters[0]
    count = int(plan["estimated_layers"])
    assignments: dict[str, list[dict]] = defaultdict(list)
    for allocation in plan.get("device_allocations") or []:
        node = allocation["node_id"]
        if node not in members:
            raise ValueError(f"Selected Prima participant {node} is no longer enrolled")
        if allocation["layer_end"] > allocation["layer_start"]:
            assignments[node].append(allocation)
    cpu_layers = int(plan.get("cpu_layers") or 0)
    if cpu_layers:
        assignments[master].insert(
            0,
            {
                "layer_start": 0,
                "layer_end": cpu_layers,
                "memory_kind": "ram",
            },
        )
    if not assignments and plan.get("device_effective") == "cpu":
        assignments[master] = [{"layer_start": 0, "layer_end": count, "memory_kind": "ram"}]
    # Rank one computes the first transformer layer in upstream Prima.
    workers = sorted(
        (n for n in assignments if n != master),
        key=lambda n: min(a["layer_start"] for a in assignments[n]),
    )
    nodes = [master, *workers]
    ranks: list[RankAllocation] = []
    for index, node in enumerate(nodes):
        member = members[node]
        role = "master" if node == master else "worker"
        if member.get("role") != role:
            raise ValueError(f"Prima participant {node} has changed its Kluster role")
        rows = sorted(assignments[node], key=lambda a: a["layer_start"])
        ranges: list[LayerRange] = []
        for row in rows:
            start, end = int(row["layer_start"]), int(row["layer_end"])
            if ranges and ranges[-1].end == start:
                ranges[-1] = LayerRange(start=ranges[-1].start, end=end)
            else:
                ranges.append(LayerRange(start=start, end=end))
        gpu = [a for a in rows if a.get("memory_kind", "gpu") == "gpu"]
        gpu_start = min((a["layer_start"] for a in gpu), default=count)
        if any(a["layer_end"] > gpu_start for a in rows if a.get("memory_kind") == "ram"):
            raise ValueError(f"{node}: Prima -ngl requires GPU layers after this rank's CPU layers")
        gpu_by_device: dict[str, int] = {}
        for row in gpu:
            device = str(row["device"])
            if not row.get("local", node == master):
                # RPC names encode the old transport. Resolve through this node's inventory.
                rpc_index = int(row.get("rpc_index", 0))
                devices = list(member.get("devices") or [])
                if rpc_index >= len(devices):
                    raise ValueError(f"{node}: selected GPU is absent from the node inventory")
                device = devices[rpc_index]["device"]
            gpu_by_device[device] = gpu_by_device.get(device, 0) + int(row["layers"])
        ngl = sum(gpu_by_device.values())
        ranks.append(
            RankAllocation(
                node_id=node,
                role=role,
                rank=index,
                address=str(
                    member.get("rpc_bind")
                    or member.get("host")
                    or member.get("lan_host")
                    or "127.0.0.1"
                ),
                next_node_id=nodes[(index + 1) % len(nodes)],
                layers=ranges,
                ngl=ngl,
                devices=list(gpu_by_device),
                tensor_split=[float(value / ngl) for value in gpu_by_device.values()]
                if ngl
                else [],
            )
        )
    return PrimaTopology(
        runtime_id=runtime_id,
        master_node_id=master,
        total_layers=count,
        ranks=ranks,
    )


def layer_windows(topology: PrimaTopology) -> list[int]:
    """Only encode allocations expressible by one exact Prima window cycle.

    Arbitrary CPU-prefix/master-suffix and expert placements cannot be converted
    by rounding tensor proportions; reject them instead of changing the plan.
    """
    end = 0
    order = topology.ranks[1:] + topology.ranks[:1] if len(topology.ranks) > 1 else topology.ranks
    for rank in order:
        if not rank.layers:
            continue
        if len(rank.layers) != 1 or rank.layers[0].start != end:
            raise ValueError(
                "Prima window order cannot represent the approved Kluster layer placement"
            )
        end = rank.layers[0].end
    if end != topology.total_layers:
        raise ValueError("Prima layer windows do not cover the approved model")
    return [sum(r.end - r.start for r in rank.layers) for rank in topology.ranks]


def rank_launch(topology: PrimaTopology, node_id: str) -> dict[str, Any]:
    rank = next((r for r in topology.ranks if r.node_id == node_id), None)
    if rank is None:
        raise ValueError("Node is not selected for this Prima run")
    windows = layer_windows(topology)
    following = topology.ranks[(rank.rank + 1) % len(topology.ranks)]
    environment = {"KLLM_PRIMA_BIND": rank.address}
    vulkan_devices = [
        device.removeprefix("Vulkan")
        for device in rank.devices
        if device.startswith("Vulkan") and device.removeprefix("Vulkan").isdigit()
    ]
    if vulkan_devices:
        environment["GGML_VK_VISIBLE_DEVICES"] = ",".join(vulkan_devices)
    arguments = [
        "-ngl",
        str(rank.ngl),
        "--world",
        str(len(topology.ranks)),
        "--rank",
        str(rank.rank),
        "--master",
        topology.ranks[0].address,
        "--next",
        following.address,
        "--data-port",
        str(topology.data_port),
        "--signal-port",
        str(topology.signal_port),
        "-lw",
        ",".join(str(v) for v in windows),
    ]
    if rank.tensor_split:
        arguments.extend(("-ts", ",".join(format(v, ".12g") for v in rank.tensor_split)))
    return {
        "ngl": rank.ngl,
        "tensor_split": ",".join(format(v, ".12g") for v in rank.tensor_split),
        "devices": rank.devices,
        "env": environment,
        "extra_args": arguments,
    }


def network_edges(topology: PrimaTopology) -> list[dict]:
    """Exact connectivity to admit: ring data, worker replies and head signals."""
    if len(topology.ranks) == 1:
        return []
    edges = []
    head = topology.ranks[0]
    for rank in topology.ranks:
        following = topology.ranks[(rank.rank + 1) % len(topology.ranks)]
        edges.append(
            {
                "source": rank.node_id,
                "target": following.node_id,
                "address": following.address,
                "port": topology.data_port + following.rank,
            }
        )
        if rank.rank:
            edges.extend(
                [
                    {
                        "source": rank.node_id,
                        "target": head.node_id,
                        "address": head.address,
                        "port": topology.data_port,
                    },
                    {
                        "source": head.node_id,
                        "target": rank.node_id,
                        "address": rank.address,
                        "port": topology.signal_port + rank.rank,
                    },
                ]
            )
    return [dict(t) for t in dict.fromkeys(tuple(e.items()) for e in edges)]
