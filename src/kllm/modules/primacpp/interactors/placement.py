"""Translate Kluster placement without changing layer ownership or device order."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from kllm.modules.primacpp.entities import LayerRange, PrimaTopology, RankAllocation
from kllm.modules.primacpp.interactors.platforms import normalize_platform, require_shared_ring_backend


def topology_from_plan(plan: dict, members: dict, runtime_id: str) -> PrimaTopology:
    if plan.get("fit_status") == "overflow":
        raise ValueError("Cannot create a Prima execution topology for an overflowing plan")
    if (plan.get("split_mode") or "layer") != "layer":
        raise ValueError("Prima row/tensor execution needs compatibility implementation")
    if plan.get("tensor_overrides") or plan.get("expert_offloaded"):
        raise ValueError("Prima remote MoE tensor placement needs compatibility implementation")
    masters = [node for node, member in members.items() if member.get("role") == "master"]
    if len(masters) != 1:
        raise ValueError("Prima requires exactly one current Kluster master")
    master = masters[0]
    count = int(plan["estimated_layers"])
    assignments: dict[str, list[dict]] = defaultdict(list)
    output = None
    for allocation in plan.get("device_allocations") or []:
        node = allocation["node_id"]
        if node not in members:
            raise ValueError(f"Selected Prima participant {node} is no longer enrolled")
        if allocation["layer_end"] > allocation["layer_start"]:
            if allocation["layer_end"] > count:
                if output is not None:
                    raise ValueError("Prima output stage has multiple owners")
                if allocation["layer_end"] != count + 1 or node != master:
                    raise ValueError("Prima output stage must remain on the rank-zero master")
                output = allocation
            end = min(allocation["layer_end"], count)
            if allocation["layer_start"] < end:
                assignments[node].append({**allocation, "layer_end": end, "layers": end - allocation["layer_start"]})
    cpu_layers = min(int(plan.get("cpu_layers") or 0), count)
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
            if row.get("memory_kind", "gpu") not in ("gpu", "ram"):
                raise ValueError(f"{node}: Prima layer allocation has an unknown memory kind")
            if "layers" in row and (type(row["layers"]) is not int or row["layers"] != end - start):
                raise ValueError(f"{node}: Prima layer allocation count differs from its range")
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
                # RPC device indices include CPU devices and need not match the
                # worker's accelerator-list positions. Resolve the named device.
                rpc_index = int(row.get("rpc_index", 0))
                devices = list(member.get("devices") or [])
                rpc_devices = list(member.get("rpc_devices") or [])
                if rpc_devices:
                    selected = [entry for entry in rpc_devices if entry.get("index") == rpc_index
                                and entry.get("memory_kind") == "gpu"]
                    if len(selected) != 1 or not any(entry.get("device") == selected[0].get("device")
                                                      for entry in devices):
                        raise ValueError(f"{node}: selected RPC GPU differs from the node inventory")
                    device = str(selected[0]["device"])
                elif not 0 <= rpc_index < len(devices):
                    raise ValueError(f"{node}: selected GPU is absent from the node inventory")
                else:
                    device = str(devices[rpc_index]["device"])
            if device in gpu_by_device and device != next(reversed(gpu_by_device)):
                raise ValueError(f"{node}: Prima cannot represent interleaved GPU device layers exactly")
            gpu_by_device[device] = gpu_by_device.get(device, 0) + row["layer_end"] - row["layer_start"]
        ngl = sum(gpu_by_device.values())
        output_gpu = output is not None and output.get("memory_kind", "gpu") == "gpu" and node == master
        if output_gpu:
            output_device = str(output["device"])
            if not gpu_by_device:
                gpu_by_device[output_device] = 0
            if len(gpu_by_device) != 1 or output_device not in gpu_by_device:
                raise ValueError("Prima output GPU placement requires the master's single selected device")
        # Native upper_bound compares integer position/count to a cumulative
        # split. Whole-layer weights preserve each device boundary exactly.
        if len(gpu_by_device) > 1:
            split = [float(value) for value in gpu_by_device.values()]
        elif gpu_by_device:
            split = [1.0]
        else:
            split = []
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
                tensor_split=split,
                output_gpu=output_gpu,
            )
        )
    # Only ranks that offload to a GPU constrain the ring; a CPU-only coordinator does not.
    require_shared_ring_backend([
        (rank.node_id, normalize_platform(members[rank.node_id].get("platform")),
         members[rank.node_id].get("actual_backend") or members[rank.node_id].get("backend"))
        for rank in ranks if rank.ngl or rank.output_gpu
    ])
    return PrimaTopology(
        runtime_id=runtime_id,
        master_node_id=master,
        total_layers=count,
        ranks=ranks,
    )


def layer_counts(topology: PrimaTopology) -> list[int]:
    """Validate absolute ownership independently of the legacy window order."""
    counts = []
    ranges = []
    for index, rank in enumerate(topology.ranks):
        if rank.rank != index:
            raise ValueError("Prima ranks must retain their approved ring order")
        previous = 0
        count = 0
        for window in rank.layers:
            if window.start < previous or window.end <= window.start or window.end > topology.total_layers:
                raise ValueError("Prima rank ranges must be ordered and bounded")
            previous = window.end
            count += window.end - window.start
            ranges.append((window.start, window.end))
        counts.append(count)
    end = 0
    for start, stop in sorted(ranges):
        if start != end:
            raise ValueError("Prima absolute ownership has a gap or overlap")
        end = stop
    if end != topology.total_layers:
        raise ValueError("Prima absolute ownership does not cover the approved model")
    return counts


def layer_windows(topology: PrimaTopology) -> list[int]:
    """Only encode allocations expressible by one exact Prima window cycle.

    Arbitrary CPU-prefix/master-suffix and expert placements cannot be converted
    by rounding tensor proportions; reject them instead of changing the plan.
    """
    counts = layer_counts(topology)
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
    return counts


def graph_visits(topology: PrimaTopology) -> dict[str, int]:
    """Count native graph visits from absolute ownership, including head graphs."""
    layer_counts(topology)
    counts = {rank.node_id: 2 if rank.rank == 0 else 0 for rank in topology.ranks}
    previous = None
    for _, _, node in sorted(
        (window.start, window.end, rank.node_id)
        for rank in topology.ranks for window in rank.layers
    ):
        if node != previous:
            counts[node] += 1
        previous = node
    return counts


def require_layer_execution(topology: PrimaTopology, features: set[str], settings: dict) -> None:
    """Check exact native window or segmented graph capacity before preview/launch."""
    layer_counts(topology)
    if "segmented_placement" not in features:
        layer_windows(topology)
        return
    try:
        layer_windows(topology)
    except ValueError:
        expected = graph_visits(topology)
        counts = settings.get("prima_graph_counts")
        if (settings.get("prima_graphs") is not True or not isinstance(counts, dict)
                or set(counts) != set(expected)
                or any(type(counts[node]) is not int or counts[node] != value
                       for node, value in expected.items())):
            raise ValueError("Segmented Prima execution requires exact planned per-visit graph reservations") from None


def rank_launch(topology: PrimaTopology, node_id: str, *, segmented: bool = False) -> dict[str, Any]:
    rank = next((r for r in topology.ranks if r.node_id == node_id), None)
    if rank is None:
        raise ValueError("Node is not selected for this Prima run")
    windows = (layer_counts if segmented else layer_windows)(topology)
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
